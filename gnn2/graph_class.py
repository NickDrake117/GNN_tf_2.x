# coding=utf-8

import os

import numpy as np
from scipy.sparse import coo_matrix

#######################################################################################################################
## GRAPH OBJECT CLASS #################################################################################################
#######################################################################################################################
class GraphObject:
    ## CONSTRUCTORS METHODS ###########################################################################################
    def __init__(self, arcs, nodes, targets,
                 problem_based: str = 'n',
                 set_mask=None,
                 output_mask=None,
                 NodeGraph=None,
                 ArcNode=None,
                 node_aggregation: str = "average"):
        """ CONSTRUCTOR METHOD

        :param arcs: Ordered Arcs Matrix where arcs[i] = [ID Node From | ID NodeTo | Arc Label].
        :param nodes: Ordered Nodes Matrix where nodes[i] = [Node Label].
        :param targets: Targets Array with shape (Num of targeted example [nodes or arcs], dim_target example).
        :param problem_based: (str) define the problem on which graph is used: 'a' arcs-based, 'g' graph-based, 'n' node-based.
        :param set_mask: Array of {0,1} to define arcs/nodes belonging to a set, when dataset == single GraphObject.
        :param output_mask: Array of {0,1} to define the sub-set of arcs/nodes whose target is known.
        :param NodeGraph: Matrix (nodes.shape[0],{Num graphs or 1}) used only when problem_based=='g'.
        :param ArcNode: Matrix of shape (num_of_arcs, num_of_nodes) s.t. A[i,j]=value if arc[i,2]==node[j].
        :param node_aggregation: Define how graph-based problem are faced. Possible values in ['average', 'sum', 'normalized'].
        """
        # store arcs, nodes, targets
        self.arcs = arcs.astype('float32')
        self.nodes = nodes.astype('float32')
        self.targets = targets.astype('float32')
        
        # store dimensions
        self.DIM_NODE_LABEL = nodes.shape[1]
        self.DIM_ARC_LABEL = (arcs.shape[1] - 2)  # first two columns contain nodes indices
        self.DIM_TARGET = targets.shape[1]
        
        # setting the problem type: node, arcs or graph based + check existence of passed parameters in keys
        lenMask = {'n': nodes.shape[0], 'a': arcs.shape[0], 'g': nodes.shape[0]}
        
        # build set_mask, for a dataset composed of only a single graph: its nodes have to be divided in Tr, Va and Te
        self.set_mask = np.ones(lenMask[problem_based], dtype=bool) if set_mask is None else set_mask.astype(bool)
        # build output_mask
        self.output_mask = np.ones(len(self.set_mask), dtype=bool) if output_mask is None else output_mask.astype(bool)
        
        # check lengths: output_mask must be as long as set_mask
        if len(self.set_mask) != len(self.output_mask): raise ValueError('Error - len(<set_mask>) != len(<output_mask>)')
            
        # build Adjancency Matrix
        self.Adjacency = self.buildAdiacencyMatrix()
        
        # build ArcNode tensor or acquire it from input
        self.ArcNode = self.buildArcNode(node_aggregation=node_aggregation) if ArcNode is None else ArcNode.astype('float32')
        
        # build node_graph conversion matrix
        self.NodeGraph = self.buildNodeGraph(problem_based) if NodeGraph is None else NodeGraph.astype('float32')

    # -----------------------------------------------------------------------------------------------------------------
    def copy(self):
        """ COPY METHOD

        :return: a Deep Copy of the GraphObject instance.
        """
        return GraphObject(arcs=self.getArcs(), nodes=self.getNodes(), targets=self.getTargets(), set_mask=self.getSetMask(),
                           output_mask=self.getOutputMask(), NodeGraph=self.getNodeGraph(), ArcNode=self.getArcNode())

    # -----------------------------------------------------------------------------------------------------------------
    def buildAdiacencyMatrix(self):
        """ Build Adjacency Matrix ADJ of the graph, s.t. ADJ[i,j]=1 if edge (i,j) exists in graph edges set. """
        values = np.ones(self.arcs.shape[0], dtype='float32')
        indices = self.arcs[:, :2].astype(int)
        return coo_matrix((values, (indices[:, 0], indices[:, 1])), shape=(self.nodes.shape[0], self.nodes.shape[0]), dtype='float32')

    # -----------------------------------------------------------------------------------------------------------------
    def buildArcNode(self, node_aggregation: str):
        """ Build ArcNode Matrix A of shape (number_of_arcs, number_of_nodes) where A[i,j]=value if arc[i,2]==node[j].

        Compute the matmul(m:=message,A) to get the incoming message on each node.

        :param node_aggregation: (str) It defines the aggregation mode for the incoming message of a node:
            > 'average': elem(A)={0-1} -> matmul(m,A) gives the average of incoming messages, s.t. sum(A[:,i])=1;
            > 'normalized': elem(A)={0-1} -> matmul(m,A) gives the normalized message wrt the total number of g.nodes;
            > 'sum': elem(A)={0,1} -> matmul(m,A) gives the total sum of incoming messages.
        :return: sparse ArcNode Matrix, for memory efficiency.
        :raise: Error if <node_aggregation> is not in ['average','sum','normalized'].
        """
        if node_aggregation not in ['average', 'normalized', 'sum']: raise ValueError("ERROR: Unknown aggregation mode")
        col = self.arcs[:, 1]  # column indices of A are located in the second column of the arcs tensor
        row = np.arange(0, len(col))  # arc id (from 0 to number of arcs)
        val, col_index, destination_node_counts = np.unique(col, return_inverse=True, return_counts=True)
        
        # sum node aggregation - incoming message as sum of neighbors states and labels
        values_vector = np.ones(len(col))
        
        # average node aggregation - incoming message as average of neighbors states and labels
        if node_aggregation == "average":
            values_vector = values_vector / destination_node_counts[col_index]
        
        # normalized node aggregation - incoming message as sum of neighbors states and labels divided by the number of nodes in the graph
        if node_aggregation == "normalized":
            values_vector = values_vector * float(1 / len(col))
            
        # isolated nodes correction: if nodes[i] is isolated, then ArcNode[:,i]=0, to maintain nodes ordering
        return coo_matrix((values_vector, (row, self.arcs[:, 1])), shape=(self.arcs.shape[0], self.nodes.shape[0]), dtype='float32')

    # -----------------------------------------------------------------------------------------------------------------
    def setArcNode(self, node_aggregation: str):
        """ Set ArcNode values for the specified node_aggregation argument """
        self.ArcNode = self.buildArcNode(node_aggregation=node_aggregation)

    # -----------------------------------------------------------------------------------------------------------------
    def buildNodeGraph(self, problem_based: str):
        """ Build Node-Graph Aggregation Matrix, to transform a node-based problem in a graph-based one.
        nodegraph != None only if problem_based == 'g': It has dimensions (nodes.shape[0], 1) for a single graph, 
        or (nodes.shape[0], Num graphs) for a graph containing 2+ graphs, built by merging the single graphs into a bigger one,
        such that after the node-graph aggregation process gnn can compute (Num graphs, targets.shape[1]) as output.
        It's normalized wrt the number of nodes whose output is computed, i.e. the number of ones in output_mask.
        :return: nodegraph matrix if :param problem_based: is 'g' else None, as nodegraph is used in graph-based problems.
        """
        nodegraph = None
        if problem_based == 'g':
            nodes_output_coefficient = self.nodes.shape[0]
            nodegraph = np.ones((nodes_output_coefficient, 1), dtype=np.float32) * 1 / nodes_output_coefficient
        return nodegraph

    # -----------------------------------------------------------------------------------------------------------------
    def save(self, graph_folder_path: str) -> None:
        """ save graph in folder. All attributes are saved in numpy .npy files.

        :param graph_folder_path: (str) folder path in which graph is saved.
        """
        GraphObject.save_graph(graph_folder_path, self)

    # -----------------------------------------------------------------------------------------------------------------
    def savetxt(self, graph_folder_path: str, format: str = '%.10g') -> None:
        """ save graph in folder. All attributes are saved in textual .txt files.

        :param graph_folder_path: (str) folder path in which graph is saved.
        """
        GraphObject.save_txt(graph_folder_path, self, format)

    ## GETTERS ########################################################################################################
    def getArcs(self):
        return self.arcs.copy()

    def getNodes(self):
        return self.nodes.copy()

    def getTargets(self):
        return self.targets.copy()

    def getSetMask(self):
        return self.set_mask.copy()

    def getOutputMask(self):
        return self.output_mask.copy()

    def getAdjacency(self):
        return self.Adjacency.copy()

    def getArcNode(self):
        return self.ArcNode.copy()

    def getNodeGraph(self):
        return None if self.NodeGraph is None else self.NodeGraph.copy()


    ## CLASS METHODs ##################################################################################################
    @classmethod
    def save_graph(self, graph_folder_path: str, g):
        """ Save a graph to a directory, creating txt files referring to all attributes of graph g
        Note that graph_folder_path will contain ONLY a single graph g. If folder is not empty, it is removed and re-made
        Remind that dataset folder contains one folder for each graph.

        :param graph_folder_path: new directory for saving the graph. 
        :param g: graph of type GraphObject to be saved.
        """
        import shutil
        
        # check folder
        if graph_folder_path[-1] != '/': graph_folder_path += '/'
        if os.path.exists(graph_folder_path): shutil.rmtree(graph_folder_path)
        os.makedirs(graph_folder_path)
        
        # save everything
        np.save(graph_folder_path + "arcs.npy", g.arcs)
        np.save(graph_folder_path + "nodes.npy", g.nodes)
        np.save(graph_folder_path + "targets.npy", g.targets)
        if not all(g.set_mask): np.save(graph_folder_path + 'set_mask.npy', g.set_mask)
        if not all(g.output_mask): np.save(graph_folder_path + "output_mask.npy", g.output_mask)
        if g.NodeGraph is not None and g.targets.shape[0] > 1: np.save(graph_folder_path + 'NodeGraph.npy', g.NodeGraph)

    # -----------------------------------------------------------------------------------------------------------------
    @classmethod
    def save_txt(self, graph_folder_path: str, g, format: str = '%.10g'):
        """ Save a graph to a directory, creating txt files referring to all attributes of graph g
        Note that graph_folder_path will contain ONLY a single graph g. If folder is not empty, it is removed and re-made.
        Remind that dataset folder contains one folder for each graph.

        :param graph_folder_path: new directory for saving the graph.
        :param g: graph of type GraphObject to be saved.
        :param format: param passed to np.savetxt().
        """
        import shutil
        
        # check folder
        if graph_folder_path[-1] != '/': graph_folder_path += '/'
        if os.path.exists(graph_folder_path): shutil.rmtree(graph_folder_path)
        os.makedirs(graph_folder_path)
        
        # save everything
        np.savetxt(graph_folder_path + "arcs.txt", g.arcs, fmt=format, delimiter=',')
        np.savetxt(graph_folder_path + "nodes.txt", g.nodes, fmt=format, delimiter=',')
        np.savetxt(graph_folder_path + "targets.txt", g.targets, fmt=format, delimiter=',')
        if not all(g.set_mask): np.savetxt(graph_folder_path + 'set_mask.txt', g.set_mask, fmt=format, delimiter=',')
        if not all(g.output_mask): np.savetxt(graph_folder_path + "output_mask.txt", g.output_mask, fmt=format, delimiter=',')
        if g.NodeGraph is not None and g.targets.shape[0] > 1: np.savetxt(graph_folder_path + 'NodeGraph.txt', g.NodeGraph, fmt=format,
                                                                          delimiter=',')

    # -----------------------------------------------------------------------------------------------------------------
    @classmethod
    def load(self, graph_folder_path: str, problem_based: str, node_aggregation: str):
        """ Load a graph from a directory which contains at least 3 numpy files referring to nodes, arcs and targets

        :param graph_folder_path: directory containing at least 3 files: 'nodes.npy', 'arcs.npy' and 'targets.npy'
            > other possible files: 'NodeGraph.npy','output_mask.npy' and 'set_mask.npy'. No other files required!
        :param node_aggregation: node aggregation mode: 'average','sum','normalized'. Go to BuildArcNode for details
        :param problem_based: (str) : 'n'-nodeBased; 'a'-arcBased; 'g'-graphBased
            > NOTE For graph_based problems, file 'NodeGraph.npy' must be present in folder
        :return: GraphObject described by files in <graph_folder_path> folder
        """      
        # load all the files inside <graph_folder_path> folder
        if graph_folder_path[-1] != '/': graph_folder_path += '/'
        files = os.listdir(graph_folder_path)
        keys = [i.rsplit('.')[0] for i in files] + ['problem_based', 'node_aggregation']
        vals = [np.load(graph_folder_path + i) for i in files] + [problem_based, node_aggregation]
        
        # create a dictionary with parameters and values to be passed to constructor and return GraphObject
        params = dict(zip(keys, vals))
        return self(**params)

    # -----------------------------------------------------------------------------------------------------------------
    @classmethod
    def load_txt(self, graph_folder_path: str, problem_based: str, node_aggregation: str):
        """ Load a graph from a directory which contains at least 3 txt files referring to nodes, arcs and targets

        :param graph_folder_path: directory containing at least 3 files: 'nodes.txt', 'arcs.txt' and 'targets.txt'
            > other possible files: 'NodeGraph.txt','output_mask.txt' and 'set_mask.txt'. No other files required!
        :param problem_based: (str) : 'n'-nodeBased; 'a'-arcBased; 'g'-graphBased
            > NOTE For graph_based problems, file 'NodeGraph.txt' must to be present in folder
        :param node_aggregation: node aggregation mode: 'average','sum','normalized'. Go to BuildArcNode for details
        :return: GraphObject described by files in <graph_folder_path> folder
        """
        # load all the files inside <graph_folder_path> folder
        if graph_folder_path[-1] != '/': graph_folder_path += '/'
        files = os.listdir(graph_folder_path)
        keys = [i.rsplit('.')[0] for i in files] + ['problem_based', 'node_aggregation']
        vals = [np.loadtxt(graph_folder_path + i, delimiter=',', ndmin=2) for i in files] + [problem_based, node_aggregation]
        
        # create a dictionary with parameters and values to be passed to constructor and return GraphObject
        params = dict(zip(keys, vals))
        return self(**params)

    # -----------------------------------------------------------------------------------------------------------------
    @classmethod
    def merge(self, glist, problem_based: str, node_aggregation: str):
        """ Method to merge graphs: it takes in input a list of graphs and returns them as a single graph

        :param glist: list of GraphObjects
            > NOTE if problem_based=='g', new NodeGraph will have dimension (Num nodes, Num graphs) else None
        :param node_aggregation: str, node aggregation mode for new GraphObject, go to buildArcNode for details
        :return: a new GraphObject containing all the information (nodes, arcs, targets, etc) in glist
        """
        # check parameters
        if not (type(glist) == list and all(isinstance(x, (GraphObject, str)) for x in glist)):
            raise TypeError('type of param <glist> must be list of str \'path-like\' or GraphObjects')

        nodes, nodes_lens, arcs, targets, set_mask, output_mask, nodegraph_list = zip(
            *[(i.getNodes(), i.nodes.shape[0], i.getArcs(), i.getTargets(),
               i.getSetMask(), i.getOutputMask(), i.getNodeGraph()) for i in glist])
        
        # get single matrices for new graph
        for i, elem in enumerate(arcs): elem[:, :2] += sum(nodes_lens[:i])
        arcs = np.concatenate(arcs, axis=0)
        nodes = np.concatenate(nodes, axis=0)
        targets = np.concatenate(targets, axis=0)
        set_mask = np.concatenate(set_mask, axis=0)
        output_mask = np.concatenate(output_mask, axis=0)

        nodegraph = None
        if problem_based == 'g':
            from scipy.linalg import block_diag
            nodegraph = block_diag(*nodegraph_list)

        # resulting GraphObject
        return self(arcs=arcs, nodes=nodes, targets=targets, set_mask=set_mask, output_mask=output_mask,
                    problem_based=problem_based, NodeGraph=nodegraph, node_aggregation=node_aggregation)
